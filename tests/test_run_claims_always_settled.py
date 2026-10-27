"""Claimed incident work always ends recorded (audit 2026-10-26 AUD-018).

A decision claims its incident (``acting``), a card send claims its outbox
(``notify = 'sending'``), a diagnosis claims ``diagnosis_state = 'running'``.
Each caught only RuntimeError, OSError and ValueError around the work, so a
"database is locked" (``sqlite3.Error``), an httpx error, a KeyError or a
TypeError escaped and left the claim for good: the incident could neither be
acted on nor expire, the unique index blocked a new incident for the run, and
the next start recorded "Kazma stopped while the action ran". Now the failure
classes met in practice are answered as failed outcomes
(``claims.WORK_FAILURES``), and anything else settles the claim before it
propagates (``claims.settle_on_error``).

The real decision, delivery and diagnosis code on a private incident store,
with a fake kind whose action raises.

Negative control: the old decision tail (the three types caught), run inline,
lets a "database is locked" escape and leaves the row ``acting``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from kazma_core.runs import diagnose, notify, registry
from kazma_core.runs.contract import ActionResult, Run, RunState
from kazma_core.runs.decide import decide
from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
from kazma_core.runs.kinds import KINDS

T0 = 1_800_000_000.0


class _UnforeseenError(Exception):
    """A failure class no handler names."""


class _Kind:
    STATE_MAP = {"failed": RunState.FAILED}

    def __init__(self) -> None:
        self.raises: BaseException | None = None

    async def list_runs(self, now, *, window_s):
        return [Run(run_id="swarm_task:t1", kind="swarm_task", native_id="t1", title="research",
                    state=RunState.FAILED, native_state="failed", ended_at=T0, actions=("retry",), version="1",
                    origin={"platform": "telegram", "target": "telegram:42", "thread_id": "gw"})]

    async def act(self, native_id, action, *, actor):
        if self.raises is not None:
            raise self.raises
        return ActionResult(True, "retried", "ok")


@pytest.fixture
def world(tmp_path, monkeypatch):
    kind = _Kind()
    for name in list(KINDS):
        if name != "swarm_task":
            monkeypatch.delitem(KINDS, name)
    monkeypatch.setitem(registry._ADAPTERS, "swarm_task", kind)
    store = IncidentStore(tmp_path / "inc.db")
    reset_incident_store(store)
    run = asyncio.run(kind.list_runs(T0, window_s=1))[0]
    row, created = store.open_incident(run, "failed", origin=dict(run.origin), now=T0)
    assert created
    yield SimpleNamespace(kind=kind, store=store, row=row)
    reset_incident_store(None)


def _state(world) -> dict:
    return get_incident_store().get(world.row["id"])


# ── decisions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [sqlite3.OperationalError("database is locked"), KeyError("task"),
                                 TypeError("bad row"), httpx.ConnectError("no route")])
def test_a_known_failure_of_the_action_is_recorded(world, exc):
    world.kind.raises = exc
    outcome = asyncio.run(decide(world.row["id"], "retry", actor="owner", platform="telegram"))
    assert not outcome.ok and outcome.code == "action_failed"
    row = _state(world)
    assert row["state"] == "open", "the owner can choose again; never left acting"
    assert row["outcome_code"] == type(exc).__name__


def test_an_unforeseen_failure_settles_the_claim_and_goes_on(world):
    world.kind.raises = _UnforeseenError("nobody named me")
    with pytest.raises(_UnforeseenError):
        asyncio.run(decide(world.row["id"], "retry", actor="owner", platform="telegram"))
    row = _state(world)
    assert row["state"] == "open" and row["outcome_code"] == "_UnforeseenError"


def test_a_cancelled_action_stays_acting_for_the_restart_to_close(world):
    """Cancellation is not a failure: recovery at the next start decides it."""
    world.kind.raises = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(decide(world.row["id"], "retry", actor="owner", platform="telegram"))
    assert _state(world)["state"] == "acting"


def test_negative_control_the_old_tail_leaves_the_row_acting(world):
    from kazma_core.runs.registry import perform

    world.kind.raises = sqlite3.OperationalError("database is locked")
    store = get_incident_store()

    async def old_decide(incident_id, action):
        row = await asyncio.to_thread(store.claim_decision, incident_id, action, actor="owner", platform="t")
        try:
            result = await perform(row["run_id"], action, actor="owner")
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, ValueError) as exc:  # the handler until 2026-10-26
            return await asyncio.to_thread(store.finish_decision, incident_id, ok=False,
                                           code=type(exc).__name__, message=str(exc))
        return await asyncio.to_thread(store.finish_decision, incident_id, ok=result.ok, code=result.code,
                                       message=result.message)

    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(old_decide(world.row["id"], "retry"))
    assert _state(world)["state"] == "acting", "stuck: no action, no expiry, no new incident"


# ── the card's send ─────────────────────────────────────────────────────

def _ops_refuses(monkeypatch) -> list[str]:
    import importlib

    asked: list[str] = []

    def alert(key, title, detail="", **kw):
        asked.append(key)
        return False

    monkeypatch.setattr(importlib.import_module("kazma_core.observability.ops_alerts"), "alert", alert)
    return asked


def test_a_send_that_raises_a_known_failure_falls_through_and_is_settled(world, monkeypatch):
    async def send(target, text, *, backend="telegram", **kw):
        raise httpx.ReadTimeout("telegram did not answer")

    monkeypatch.setattr(notify, "_send", send)
    asked = _ops_refuses(monkeypatch)
    assert asyncio.run(notify.deliver(world.row)) == "refused"
    assert asked == [f"runs.failed.{world.row['id']}"], "the ops channels were tried"
    assert _state(world)["notify"] == "refused"


def test_a_send_that_raises_an_unforeseen_failure_is_uncertain(world, monkeypatch):
    async def send(target, text, *, backend="telegram", **kw):
        raise _UnforeseenError("mid-send")

    monkeypatch.setattr(notify, "_send", send)
    _ops_refuses(monkeypatch)
    with pytest.raises(_UnforeseenError):
        asyncio.run(notify.deliver(world.row))
    row = _state(world)
    assert row["notify"] == "uncertain", "it may have reached the chat: never resent, never left sending"
    assert "_UnforeseenError" in row["notify_detail"]


# ── the diagnosis ───────────────────────────────────────────────────────

def _no_follow_up(monkeypatch) -> None:
    async def nothing(row, text):
        return None

    monkeypatch.setattr(notify, "send_diagnosis", nothing)


def test_a_diagnosis_that_raises_a_known_failure_is_recorded(world, monkeypatch):
    def builder():
        raise KeyError("graph")

    monkeypatch.setattr(diagnose, "_graph_builder", builder)
    _no_follow_up(monkeypatch)
    assert get_incident_store().claim_diagnosis(world.row["id"])
    asyncio.run(diagnose._diagnose(world.row, actor="owner", platform="telegram"))
    row = _state(world)
    assert row["diagnosis_state"] == "failed" and "KeyError" in row["diagnosis"]


def test_a_diagnosis_that_raises_an_unforeseen_failure_is_settled(world, monkeypatch):
    def builder():
        raise _UnforeseenError("graph")

    monkeypatch.setattr(diagnose, "_graph_builder", builder)
    _no_follow_up(monkeypatch)
    assert get_incident_store().claim_diagnosis(world.row["id"])
    with pytest.raises(_UnforeseenError):
        asyncio.run(diagnose._diagnose(world.row, actor="owner", platform="telegram"))
    row = _state(world)
    assert row["diagnosis_state"] == "failed", "never left running: a new diagnosis can start"
    assert get_incident_store().claim_diagnosis(world.row["id"])
