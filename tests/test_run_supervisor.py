"""The run supervisor tells the owner once, about the right thing, at the right time.

Phase 2 of ``docs/plans/RUN_SUPERVISION_PLAN.md``. A fake kind on a fake clock
drives the real supervisor, the real incident store (a private file) and the
real delivery code; only the platform send and the ops-alert channel are
recorders. Each rule has its negative control.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest
from kazma_core.runs import contract, registry, supervisor
from kazma_core.runs.contract import ActionResult, KindSpec, Run, RunState, Verdict
from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
from kazma_core.runs.kinds import KINDS

T0 = 1_800_000_000.0
STALL = 600.0


class FakeAdapter:
    STATE_MAP = {"running": RunState.RUNNING, "failed": RunState.FAILED, "waiting": RunState.WAITING_HUMAN,
                 "done": RunState.DONE}

    def __init__(self) -> None:
        self.runs: list[Run] = []
        self.acted: list[tuple[str, str, str]] = []

    async def list_runs(self, now, *, window_s):
        return list(self.runs)

    async def act(self, native_id, action, *, actor):
        self.acted.append((native_id, action, actor))
        return ActionResult(True, "done", f"{action} done")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Only the fake kind exists; a private incident store; recorded sends."""
    adapter = FakeAdapter()
    for kind in list(KINDS):
        monkeypatch.delitem(KINDS, kind)
    monkeypatch.setitem(KINDS, "fake", KindSpec(kind="fake", adapter="x:y", label="Fake job", label_ar="عمل",
                                                source="test", stall_after_s=STALL, dead_after_s=120.0))
    monkeypatch.setitem(registry._ADAPTERS, "fake", adapter)
    reset_incident_store(IncidentStore(tmp_path / "incidents.db"))
    sent: list[dict] = []
    alerts: list[dict] = []

    async def fake_send(target, text, *, backend="telegram", **kw):
        sent.append({"target": target, "text": text, **kw})
        return f"sent:{target}"

    def fake_alert(key, title, detail="", **kw):
        alerts.append({"key": key, "title": title, "detail": detail, **kw})
        if kw.get("on_delivery"):
            # The ops channels settle delivery off the caller's loop.
            receipt = threading.Thread(target=kw["on_delivery"], args=(True,))
            receipt.start()
            receipt.join()
        return True

    monkeypatch.setattr("kazma_core.runs.notify._send", fake_send)
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", fake_alert)
    monkeypatch.setattr(supervisor, "_watch_started", T0 - 3600)
    monkeypatch.setattr(supervisor, "_failing_since", None)
    monkeypatch.setattr(supervisor, "_paged_unavailable", False)
    yield type("World", (), {"adapter": adapter, "sent": sent, "alerts": alerts})
    reset_incident_store(None)


def _run(ident="r1", *, state=RunState.RUNNING, native="running", progress=T0, owner=True, version="1",
         origin=None, ended=None, actions=("cancel",), effects="none") -> Run:
    return Run(run_id=f"fake:{ident}", kind="fake", native_id=ident, title=f"job {ident}", state=state,
               native_state=native, started_at=progress, progress_at=progress, owner_alive=owner,
               version=version, actions=actions, origin=origin if origin is not None else
               {"platform": "telegram", "target": "telegram:42", "thread_id": "gw-1"},
               ended_at=ended, effects=effects)


def tick(at: float) -> dict:
    return asyncio.run(supervisor.tick(now=at))


def test_a_run_without_progress_past_its_budget_is_reported_once(world):
    world.adapter.runs = [_run()]
    assert tick(T0 + STALL - 1)["opened"] == 0
    assert world.sent == []
    assert tick(T0 + STALL + 1)["opened"] == 1
    assert len(world.sent) == 1
    card = world.sent[0]["run_card"]
    assert card["actions"] == ["cancel", "dismiss", "diagnose"]
    assert "no progress" in world.sent[0]["text"]
    for later in range(3):
        tick(T0 + STALL + 31 + later * 30)
    assert len(world.sent) == 1, "one message per run and condition"


def test_a_heartbeat_only_check_would_miss_a_live_stalled_run():
    """Negative control: the owner is alive (it heartbeats), nothing moves."""
    run = _run()
    spec = KindSpec(kind="fake", adapter="x:y", label="", label_ar="", source="", stall_after_s=STALL)

    def heartbeat_only(r: Run) -> Verdict:
        return Verdict.OK if r.owner_alive else Verdict.DEAD

    assert heartbeat_only(run) == Verdict.OK
    assert contract.judge(run, spec, T0 + STALL + 1) == Verdict.STALLED


def test_a_run_waiting_for_a_human_is_never_reported(world):
    world.adapter.runs = [_run(state=RunState.WAITING_HUMAN, native="waiting")]
    tick(T0 + 10 * STALL)
    assert world.sent == [] and get_incident_store().live() == []
    # Negative control: the same run, running, would be reported.
    world.adapter.runs = [replace(world.adapter.runs[0], state=RunState.RUNNING)]
    tick(T0 + 10 * STALL + 30)
    assert len(world.sent) == 1


def test_two_stalled_runs_are_two_messages(world):
    world.adapter.runs = [_run("a"), _run("b"), _run("c", origin={}), _run("d", origin={})]
    tick(T0 + STALL + 1)
    assert len({m["run_card"]["incident_id"] for m in world.sent}) == 2
    keys = [a["key"] for a in world.alerts]
    assert len(keys) == 2 and len(set(keys)) == 2, "ops alerts are keyed per incident"
    # Negative control: ops_alerts collapses occurrences sharing a key; a key
    # naming only the condition would have turned the two into one.
    assert len({k.rsplit(".", 1)[0] for k in keys}) == 1


def test_progress_resolves_the_incident_and_says_so_once(world):
    world.adapter.runs = [_run()]
    tick(T0 + STALL + 1)
    world.adapter.runs = [_run(progress=T0 + STALL + 20)]
    counts = tick(T0 + STALL + 31)
    assert counts["resolved"] == 1
    assert len(world.sent) == 2 and "making progress again" in world.sent[1]["text"]
    tick(T0 + STALL + 61)
    assert len(world.sent) == 2


def test_a_dead_owner_is_reported_after_its_grace(world):
    world.adapter.runs = [_run(owner=False)]
    tick(T0 + 60)
    assert world.sent == []
    tick(T0 + 121)
    assert len(world.sent) == 1 and "nothing is running it" in world.sent[0]["text"]


def test_a_condition_announced_elsewhere_sends_nothing(world, monkeypatch):
    monkeypatch.setitem(KINDS, "fake", replace(KINDS["fake"], announced_by={"failed": "its own outbox"}))
    world.adapter.runs = [_run(state=RunState.FAILED, native="failed", ended=T0)]
    tick(T0 + 5)
    assert world.sent == [] and world.alerts == []
    row = get_incident_store().live()[0]
    assert row["notify"] == "suppressed" and "its own outbox" in row["notify_detail"]


def test_a_failure_from_before_this_start_is_recorded_quietly(world, monkeypatch):
    monkeypatch.setattr(supervisor, "_watch_started", T0)
    world.adapter.runs = [_run(state=RunState.FAILED, native="failed", ended=T0 - 7200)]
    tick(T0 + 5)
    assert world.sent == []
    assert get_incident_store().live()[0]["notify"] == "suppressed"
    # Negative control: a fresh failure is told.
    world.adapter.runs.append(_run("fresh", state=RunState.FAILED, native="failed", ended=T0 + 1))
    tick(T0 + 35)
    assert len(world.sent) == 1


def test_a_run_with_no_reachable_chat_goes_to_the_ops_channels(world):
    world.adapter.runs = [_run(origin={})]
    tick(T0 + STALL + 1)
    assert world.sent == []
    assert len(world.alerts) == 1 and world.alerts[0]["key"].startswith("runs.stalled.inc_")
    assert "/runs act" in world.alerts[0]["detail"]
    assert get_incident_store().live()[0]["notify"] == "delivered"


def test_an_incident_the_owner_dismissed_is_not_opened_again(world):
    from kazma_core.runs.decide import decide

    world.adapter.runs = [_run(state=RunState.FAILED, native="failed", ended=T0 + 1)]
    tick(T0 + 5)
    row = get_incident_store().live()[0]
    assert asyncio.run(decide(row["id"], "dismiss", actor="owner", platform="test")).ok
    tick(T0 + 35)
    assert get_incident_store().live() == []
    # The same run failing again under a new version is a new failure.
    world.adapter.runs = [_run(state=RunState.FAILED, native="failed", ended=T0 + 40, version="2")]
    tick(T0 + 65)
    assert len(get_incident_store().live()) == 1


def test_a_kind_that_cannot_be_read_resolves_nothing(world, monkeypatch):
    world.adapter.runs = [_run()]
    tick(T0 + STALL + 1)

    async def broken(now, *, window_s):
        raise OSError("store away")

    monkeypatch.setattr(world.adapter, "list_runs", broken)
    tick(T0 + STALL + 31)
    assert len(get_incident_store().live()) == 1, "an outage is never 'fine'"


def test_the_supervisors_own_outage_is_paged_after_five_minutes_not_at_once(world):
    boom = RuntimeError("database away")
    assert supervisor._note_pass(boom, now=T0) == "failing"
    assert supervisor._note_pass(boom, now=T0 + 299) == "failing"
    assert world.alerts == []
    assert supervisor._note_pass(boom, now=T0 + 301) == "paged"
    assert supervisor._note_pass(boom, now=T0 + 400) == "failing"
    assert [a["key"] for a in world.alerts] == ["runs.supervisor_unavailable"]
    assert supervisor._note_pass(None, now=T0 + 401) == "recovered"
    assert [a["key"] for a in world.alerts][-1] == "runs.supervisor_recovered"
    # A short blip pages nothing, and says nothing when it ends.
    assert supervisor._note_pass(boom, now=T0 + 500) == "failing"
    assert supervisor._note_pass(None, now=T0 + 530) == "ok"
    assert len(world.alerts) == 2
