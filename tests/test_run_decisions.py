"""The owner's decision on an incident: one decision, the right control, every surface.

Phase 3 of ``docs/plans/RUN_SUPERVISION_PLAN.md``:

* the first decision wins on every surface; a second one is told what was
  decided, by whom and where;
* a double press acts once;
* a press on a run that moved on does nothing and says so;
* a run whose effects are unknown never offers a plain Retry;
* every button reaches the control the kind's own page uses;
* incident decisions never touch the approval registry (they would hold a
  chat turn open, AGENTS.md §30-31);
* the chat apps route a button press to ``/runs act``, admin-only, and the
  web API answers with the same rules.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.runs import registry
from kazma_core.runs.contract import ActionResult, KindSpec, Run, RunState
from kazma_core.runs.decide import decide
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
        await asyncio.sleep(0.01)
        self.acted.append(action)
        return ActionResult(True, "done", f"{action} done")


def _run(actions=("retry",), effects="none") -> Run:
    return Run(run_id="fake:r1", kind="fake", native_id="r1", title="job", state=RunState.FAILED,
               native_state="failed", ended_at=T0, actions=actions, effects=effects, version="1")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    adapter = Recorder()
    adapter.runs = [_run()]
    monkeypatch.setitem(KINDS, "fake", KindSpec(kind="fake", adapter="x:y", label="Fake", label_ar="ع",
                                                source="test"))
    monkeypatch.setitem(registry._ADAPTERS, "fake", adapter)
    reset_incident_store(IncidentStore(tmp_path / "inc.db"))
    row, _ = get_incident_store().open_incident(adapter.runs[0], "failed", origin={}, now=T0)
    yield SimpleNamespace(adapter=adapter, incident=row)
    reset_incident_store(None)


def test_the_first_decision_wins_and_the_second_is_told(fake):
    first = asyncio.run(decide(fake.incident["id"], "retry", actor="telegram:1", platform="telegram"))
    assert first.ok and fake.adapter.acted == ["retry"]
    second = asyncio.run(decide(fake.incident["id"], "dismiss", actor="web:admin:x", platform="web"))
    assert not second.ok and second.code == "already_decided"
    assert "telegram:1" in second.message and "telegram" in second.message
    assert fake.adapter.acted == ["retry"]


def test_a_double_press_acts_once(fake):
    async def both():
        return await asyncio.gather(
            decide(fake.incident["id"], "retry", actor="a", platform="telegram"),
            decide(fake.incident["id"], "retry", actor="a", platform="telegram"))

    results = asyncio.run(both())
    assert sorted(r.ok for r in results) == [False, True]
    assert fake.adapter.acted == ["retry"]


def test_a_press_on_a_run_that_moved_on_does_nothing(fake):
    fake.adapter.runs = [_run(actions=())]  # it was retried elsewhere; Retry is gone
    outcome = asyncio.run(decide(fake.incident["id"], "retry", actor="a", platform="web"))
    assert not outcome.ok and outcome.code == "not_offered"
    assert fake.adapter.acted == []
    assert get_incident_store().get(fake.incident["id"])["state"] == "resolved"


def test_unknown_effects_never_offer_a_plain_retry(fake, monkeypatch):
    run = _run(actions=("retry_checked",), effects="unknown")
    row, _ = get_incident_store().open_incident(replace_run(run, "fake:r2"), "failed", origin={}, now=T0)
    assert "retry" not in row["actions"] and "retry_checked" in row["actions"]
    outcome = asyncio.run(decide(row["id"], "retry", actor="a", platform="web"))
    assert not outcome.ok and outcome.code == "not_offered"


def replace_run(run: Run, run_id: str) -> Run:
    from dataclasses import replace

    return replace(run, run_id=run_id, native_id=run_id.split(":", 1)[1])


def test_dismiss_leaves_the_run_alone(fake):
    outcome = asyncio.run(decide(fake.incident["id"], "dismiss", actor="a", platform="web"))
    assert outcome.ok and fake.adapter.acted == []
    assert get_incident_store().get(fake.incident["id"])["state"] == "dismissed"


def test_a_refused_action_leaves_the_incident_open(fake, monkeypatch):
    async def refuse(native_id, action, *, actor):
        return ActionResult(False, "engine_unavailable", "Swarm engine not available")

    monkeypatch.setattr(fake.adapter, "act", refuse)
    outcome = asyncio.run(decide(fake.incident["id"], "retry", actor="a", platform="web"))
    assert not outcome.ok
    row = get_incident_store().get(fake.incident["id"])
    assert row["state"] == "open" and row["outcome_message"] == "Swarm engine not available"


# ── every button reaches the page's own control ──────────────────────────

def _controls(monkeypatch) -> dict[tuple[str, str], list]:
    """Patch each kind's control with a recorder; (kind, action) -> calls."""
    calls: dict[tuple[str, str], list] = {}

    def rec(key, result):
        calls[key] = []

        async def async_fn(*a, **k):
            calls[key].append((a, k))
            return result

        def sync_fn(*a, **k):
            calls[key].append((a, k))
            return result

        return async_fn, sync_fn

    from kazma_ui.swarm_panel import task_actions

    monkeypatch.setattr(task_actions, "cancel_task", rec(("swarm_task", "cancel"), {"ok": True, "code": "cancelled"})[0])
    monkeypatch.setattr(task_actions, "retry_task", rec(("swarm_task", "retry"), {"ok": True, "code": "retried"})[0])
    cron_cancel = rec(("scheduled_job", "cancel"), {"status": "cancelled"})[0]

    async def cron_get(job_id, **k):
        return SimpleNamespace(version=3)

    monkeypatch.setattr("kazma_core.cron.scheduler.get_cron_scheduler",
                        lambda: SimpleNamespace(_store=SimpleNamespace(get=cron_get), cancel=cron_cancel))
    doc_cancel = rec(("document_job", "cancel"), {"job_id": "j", "state": "cancelled"})[1]
    doc_retry = rec(("document_job", "retry"), {"job_id": "j2", "state": "received"})[1]
    monkeypatch.setattr("kazma_core.documents.ingestion.peek_ingestion_service",
                        lambda: SimpleNamespace(cancel_job=doc_cancel, retry_job=doc_retry))
    monkeypatch.setattr("kazma_core.documents.principal.operator_for", lambda service, tenant: "operator")
    monkeypatch.setattr("kazma_core.tools.research_session.cancel_session",
                        rec(("research_session", "cancel"), SimpleNamespace(status="cancelling"))[1])
    monkeypatch.setattr("kazma_core.x_api.publication_service.update_schedule",
                        rec(("x_post", "cancel"), True)[1])
    monkeypatch.setattr("kazma_core.runs.adapters.x_posts._store_and_tenant",
                        lambda: (SimpleNamespace(get=lambda ident, tenant_id: {"version": 2}), "t"))
    monkeypatch.setattr("kazma_core.memory.task_queue.retry_failed_task",
                        rec(("memory_task", "retry"), True)[1])
    monkeypatch.setattr("kazma_ui.turn_abort.abort_turn", rec(("chat_turn", "cancel"), {"ok": True})[0])
    monkeypatch.setattr("kazma_ui.reply_sink.close_reply_turn", rec(("chat_turn", "close"), None)[1])
    from kazma_ui import runs_chat_turn

    monkeypatch.setattr(runs_chat_turn, "_graph_getter", lambda: object())
    return calls


ACTIONS = [("swarm_task", "cancel", "t1"), ("swarm_task", "retry", "t1"), ("scheduled_job", "cancel", "j1"),
           ("document_job", "cancel", "t/j1"), ("document_job", "retry", "t/j1"),
           ("research_session", "cancel", "s1"), ("x_post", "cancel", "op1"), ("memory_task", "retry", "m1"),
           ("chat_turn", "cancel", "th1"), ("chat_turn", "close", "th1")]


@pytest.mark.parametrize("kind,action,native", ACTIONS)
def test_every_action_reaches_the_kinds_own_control(monkeypatch, kind, action, native):
    calls = _controls(monkeypatch)
    result = asyncio.run(registry.adapter_for(kind).act(native, action, actor="owner"))
    assert result.ok, result
    assert len(calls[(kind, action)]) == 1


def test_the_checked_retry_tells_the_swarm_the_owner_checked(monkeypatch):
    calls = _controls(monkeypatch)
    asyncio.run(registry.adapter_for("swarm_task").act("t1", "retry_checked", actor="owner"))
    (_args, kwargs), = calls[("swarm_task", "retry")]
    assert kwargs == {"acknowledge_unknown": True}


def test_an_adapter_wired_elsewhere_is_caught(monkeypatch):
    """Negative control: an act that skips the page's control records nothing."""
    calls = _controls(monkeypatch)

    async def elsewhere(native_id, action, *, actor):
        return ActionResult(True, "done")

    monkeypatch.setattr(registry.adapter_for("swarm_task"), "act", elsewhere)
    asyncio.run(registry.adapter_for("swarm_task").act("t1", "cancel", actor="owner"))
    assert calls[("swarm_task", "cancel")] == []


def test_the_pages_use_the_same_controls():
    """The page routes call the shared functions the adapters call."""
    swarm = (REPO / "kazma-ui/kazma_ui/swarm_panel/routes_tasks.py").read_text(encoding="utf-8")
    assert "from kazma_ui.swarm_panel.task_actions import cancel_task" in swarm
    assert "from kazma_ui.swarm_panel.task_actions import retry_task" in swarm
    memory = (REPO / "kazma-ui/kazma_ui/routes_direct/memory.py").read_text(encoding="utf-8")
    assert "retry_failed_task(task_id)" in memory
    chat = (REPO / "kazma-ui/kazma_ui/sse_chat/__init__.py").read_text(encoding="utf-8")
    assert "return await abort_turn(thread_id, _get_graph(), session_id=session_id)" in chat


def test_incident_decisions_never_touch_the_approval_registry():
    """An incident is not an approval: a row in hitl_gates.db would hold a
    chat turn open (close_turn keeps a turn open on any pending gate)."""
    forbidden = re.compile(r"\b(register_gate|claim_gate|settle_gate|record_gate_decision)\(|safety\.hitl_gates")
    sources = [*sorted((REPO / "kazma-core/kazma_core/runs").rglob("*.py")),
               REPO / "kazma-gateway/kazma_gateway/run_cards.py",
               REPO / "kazma-gateway/kazma_gateway/agent_handler/runs_command.py",
               REPO / "kazma-ui/kazma_ui/runs_api.py"]
    offenders = [str(p.relative_to(REPO)) for p in sources if forbidden.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
    # Negative control: the pattern does see a gate call.
    assert forbidden.search("from kazma_core.safety.hitl_gates import register_gate")


# ── the chat apps ────────────────────────────────────────────────────────

def test_a_button_press_becomes_runs_act():
    from kazma_gateway.adapters.platform_callbacks import parse_callback_data

    action = parse_callback_data("run:retry_checked:inc_0123456789ab")
    assert action.kind == "run" and action.text == "/runs act inc_0123456789ab retry_checked"
    assert parse_callback_data("run::x").kind == "unknown"


def test_acting_from_a_chat_is_admin_only():
    from kazma_gateway.slash_commands import changes_global_config

    assert changes_global_config("/runs act inc_0123456789ab cancel")
    assert not changes_global_config("/runs")
    assert not changes_global_config("/runs show swarm_task:abc")


def test_every_platform_gates_and_routes_a_run_button():
    """Each chat app's press handler puts ``run`` under its admin gate and
    forwards it as a command."""
    telegram = (REPO / "kazma-gateway/kazma_gateway/adapters/telegram.py").read_text(encoding="utf-8")
    discord = (REPO / "kazma-gateway/kazma_gateway/adapters/discord.py").read_text(encoding="utf-8")
    slack = (REPO / "kazma-gateway/kazma_gateway/adapters/slack.py").read_text(encoding="utf-8")
    assert 'action.kind in ("hitl", "swarm", "run")' in telegram
    assert 'action.kind in ("hitl", "run")' in telegram  # the keyboard is removed at once
    assert 'action.kind in ("hitl", "swarm", "run")' in discord
    assert '"hitl", "run", "personality"' in discord
    assert 'action.kind in ("hitl", "swarm", "run")' in slack
    assert '"hitl",\n                                        "run",' in slack


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
def test_a_card_has_one_button_per_offered_action(platform):
    from kazma_core.runs.text import action_word
    from kazma_gateway.adapters.platform_callbacks import parse_callback_data
    from kazma_gateway.run_cards import apply_run_card_markup

    actions = ["cancel", "retry_checked", "dismiss", "diagnose"]
    card = {"incident_id": "inc_0123456789ab", "actions": actions, "labels": {a: action_word(a) for a in actions}}
    ctx = apply_run_card_markup({}, platform=platform, run_card=card, text="Stalled")
    if platform == "telegram":
        buttons = [(b["text"], b["callback_data"]) for row in ctx["reply_markup"]["inline_keyboard"] for b in row]
    elif platform == "discord":
        buttons = [(b["label"], b["custom_id"]) for row in ctx["components"] for b in row["components"]]
    else:
        buttons = [(b["text"]["text"], b["value"]) for blk in ctx["blocks"] if blk["type"] == "actions"
                   for b in blk["elements"]]
    pressed = [parse_callback_data(data).text.split()[-1] for _label, data in buttons]
    assert pressed == actions
    assert [label for label, _data in buttons] == [action_word(a) for a in actions], "a button says what it does"
    assert apply_run_card_markup({"x": 1}, platform=platform, run_card={"incident_id": "i", "actions": []}) == {"x": 1}


def test_runs_act_in_a_chat_decides(fake, monkeypatch):
    from kazma_gateway.agent_handler.runs_command import try_runs_command

    monkeypatch.setattr("kazma_core.security.single_operator.single_operator_refusal", lambda message: None)
    replies: list[str] = []

    async def send(text):
        replies.append(text)

    msg = SimpleNamespace(text=f"/runs act {fake.incident['id']} retry", sender_id="telegram:7", platform="telegram")
    assert asyncio.run(try_runs_command(msg, send))
    assert fake.adapter.acted == ["retry"] and replies[0].startswith("✅")
    asyncio.run(try_runs_command(msg, send))
    assert "Already handled" in replies[1] and "telegram:7" in replies[1]
    other = SimpleNamespace(text="/kb list", sender_id="x", platform="telegram")
    assert not asyncio.run(try_runs_command(other, send))


# ── the web API and the bell ─────────────────────────────────────────────

@pytest.fixture
def client(fake, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.runs_api import create_runs_router

    monkeypatch.setattr("kazma_core.security.single_operator.single_operator_refusal", lambda message: None)
    app = FastAPI()
    app.include_router(create_runs_router())
    return TestClient(app)


HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def test_the_web_answers_with_the_same_rules(client, fake, monkeypatch):
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "forbidden")
    url = f"/api/runs/incidents/{fake.incident['id']}/act"
    assert client.post(url, json={"action": "retry"}, headers=HEADERS).status_code == 403
    assert fake.adapter.acted == []
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "ok")
    assert client.post(url, json={"action": "retry"}).status_code == 403  # same-origin header missing
    first = client.post(url, json={"action": "retry"}, headers=HEADERS)
    assert first.status_code == 200 and first.json()["ok"]
    second = client.post(url, json={"action": "dismiss"}, headers=HEADERS)
    assert second.status_code == 409 and second.json()["code"] == "already_decided"
    listing = client.get("/api/runs").json()
    assert listing["ok"] and listing["incidents"][0]["id"] == fake.incident["id"]
    assert "target" not in str(listing["incidents"][0]["origin"])


def test_runbooks_are_validated_and_admin_only(client, monkeypatch):
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "forbidden")
    assert client.put("/api/runs/runbooks", json={"swarm_task.failed": True}, headers=HEADERS).status_code == 403
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "ok")
    bad = client.put("/api/runs/runbooks", json={"nope.failed": True}, headers=HEADERS)
    assert bad.status_code == 422
    good = client.put("/api/runs/runbooks", json={"swarm_task.failed": True}, headers=HEADERS)
    assert good.status_code == 200 and good.json()["runbooks"]["swarm_task.failed"] is True
    got = client.get("/api/runs/runbooks").json()["runbooks"]
    assert {b["name"]: b["enabled"] for b in got}["swarm_task.failed"] is True
    client.put("/api/runs/runbooks", json={"swarm_task.failed": False}, headers=HEADERS)


def test_the_bell_lists_open_incidents_with_a_link(fake):
    from kazma_core.runs.notify import bell_alerts

    items = bell_alerts("en")
    assert [i["callback_id"] for i in items] == [f"link:/dashboard?incident={fake.incident['id']}"]
    asyncio.run(decide(fake.incident["id"], "dismiss", actor="a", platform="web"))
    assert bell_alerts("ar") == []
