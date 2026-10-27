"""An incident settled before its card went out is never sent (audit 2026-10-26 AUD-042).

The supervisor's last step sends every incident whose outbox is ``pending``.
An incident is committed ``pending`` before its card leaves, so a process
that stopped in between leaves one for the restart -- and the restart's own
pass may first see the run recover and resolve the incident. The sweep read
``notify = 'pending'`` alone, so the owner was paged "X failed", with Retry
and Dismiss buttons, about a run that had already moved on (and the same for
one dismissed or expired meanwhile). Now only a live incident is sent, and
one a runbook closed (its message says what Kazma already did).

The real supervisor and incident store, with a fake kind.

Negative control: the old query returns the resolved incident.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from kazma_core.runs import registry, runbooks, supervisor
from kazma_core.runs.contract import Run, RunState
from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
from kazma_core.runs.kinds import KINDS

T0 = 1_800_000_000.0


class _Kind:
    STATE_MAP = {"failed": RunState.FAILED}

    def __init__(self) -> None:
        self.runs: list[Run] = []

    async def list_runs(self, now, *, window_s):
        return list(self.runs)

    async def act(self, native_id, action, *, actor):
        raise AssertionError("nothing acts here")


def _failed() -> Run:
    return Run(run_id="swarm_task:t1", kind="swarm_task", native_id="t1", title="research", state=RunState.FAILED,
               native_state="failed", ended_at=T0, actions=("retry",), version="1",
               origin={"platform": "telegram", "target": "telegram:42", "thread_id": "gw"})


@pytest.fixture
def world(tmp_path, monkeypatch):
    kind = _Kind()
    for name in list(KINDS):
        if name != "swarm_task":
            monkeypatch.delitem(KINDS, name)
    monkeypatch.setitem(registry._ADAPTERS, "swarm_task", kind)
    reset_incident_store(IncidentStore(tmp_path / "inc.db"))
    monkeypatch.setattr(runbooks, "enabled", lambda: {name: False for name in runbooks.RUNBOOK})
    sent: list[str] = []

    async def fake_send(target, text, *, backend="telegram", **kw):
        sent.append(text)
        return f"sent:{target}"

    monkeypatch.setattr("kazma_core.runs.notify._send", fake_send)
    monkeypatch.setattr(supervisor, "_watch_started", T0 - 60)
    # The previous process committed the incident and stopped before sending.
    row, created = get_incident_store().open_incident(_failed(), "failed", origin=dict(_failed().origin), now=T0)
    assert created and row["notify"] == "pending"
    yield SimpleNamespace(kind=kind, sent=sent, row=row)
    reset_incident_store(None)


def test_a_run_that_recovered_before_the_send_is_not_paged(world):
    world.kind.runs = []  # the restart sees the run gone (retried, finished)
    asyncio.run(supervisor.tick(now=T0 + 35))
    row = get_incident_store().get(world.row["id"])
    assert row["state"] == "resolved"
    assert world.sent == [], "no 'failed' card, with buttons, about a run that moved on"


def test_a_dismissed_incident_is_not_paged(world):
    get_incident_store().claim_decision(world.row["id"], "dismiss", actor="owner", platform="web")
    world.kind.runs = [_failed()]
    asyncio.run(supervisor.tick(now=T0 + 35))
    assert world.sent == []


def test_a_live_incident_left_pending_is_still_sent_once(world):
    """The restart's duty stays: the run is still failed, so the card goes."""
    world.kind.runs = [_failed()]
    asyncio.run(supervisor.tick(now=T0 + 35))
    asyncio.run(supervisor.tick(now=T0 + 65))
    assert len(world.sent) == 1
    assert get_incident_store().get(world.row["id"])["notify"] == "delivered"


def test_negative_control_the_old_sweep_returns_the_resolved_incident(world):
    store = get_incident_store()
    world.kind.runs = []
    store.resolve(world.row["id"], reason="the run is no longer listed", now=T0 + 35)
    old = store._read("SELECT * FROM run_incidents WHERE notify = 'pending' ORDER BY opened_at")
    assert [r["id"] for r in old] == [world.row["id"]], "the old sweep would page it"
    assert store.pending_notifications() == []
