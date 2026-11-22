"""Spaces S2-01: a tool list per person, within what their space may have
(plan 4.8: "X Studio on, but posting off").

Y has X Studio, with posting (``x_post``) and ``x_account`` switched off
for them on People. Those tools are then neither offered nor callable for Y
-- in chat, a scheduled job's executor and a real swarm worker -- while Y's
other X tools work, and G (who has X Studio with nothing switched off) still
has them. The check is the one the features use
(``spaces.tools.tool_refusal``), at the offer and again at the call. The
calls are made with ``x_account``: the registry runs it with no arguments,
where ``x_post`` stops at the commitment gate for everyone.

Control: hiding the tool from the offer only (the call never asks) lets
Y's call run.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from kazma_core.spaces import features, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_matrix import Matrix
from tests.test_feature_grants import _call, _offered, _registry
from tests.test_swarm_spaces import _reply, _scripted_model  # noqa: E402

pytestmark = pytest.mark.real_features

#: What People switches off for Y.
OFF = {"x_post", "x_account"}


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture
def y_without_posting(monkeypatch):
    """Y (person A) and G (person B) both have X Studio; two X tools are off for Y."""
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(scope, "_enforced", True)
    with Matrix() as matrix:
        for account in (matrix.account_a, matrix.account_b):
            people.set_features(account, {"x_studio": True}, by=OWNER_ACCOUNT_ID)
        people.set_tools(matrix.account_a, sorted(OFF), by=OWNER_ACCOUNT_ID)
        yield matrix


def test_a_tool_off_for_a_person_is_neither_offered_nor_callable_in_chat(y_without_posting) -> None:
    m = y_without_posting
    offered_y = _offered(m, m.as_a())
    assert not offered_y & OFF and {"x_status", "x_draft_posts"} <= offered_y, sorted(offered_y)
    assert OFF <= _offered(m, m.as_b()), "another person keeps them"
    for name in sorted(OFF):
        answer, entered = _call(m, m.as_a(), name)
        assert entered == [] and answer.get("refused") and "switched off" in str(answer["content"]), answer
    _answer, entered = _call(m, m.as_b(), "x_account")
    assert entered == ["x_account"]
    _answer, entered = _call(m, m.as_a(), "x_status")
    assert entered == ["x_status"], "the person's other tools of the feature work"


def test_a_tool_off_for_a_person_does_not_run_in_a_scheduled_job(y_without_posting) -> None:
    from kazma_core.agent_runner import DeclaredToolExecutor

    m = y_without_posting

    def job(registry):  # a task booked with x_account among its own tools
        return DeclaredToolExecutor(registry, allowed={"x_account", "x_status"})

    answer, entered = _call(m, m.as_a(), "x_account", job)
    assert entered == [] and answer.get("refused"), answer
    _answer, entered = _call(m, m.as_b(), "x_account", job)
    assert entered == ["x_account"]


def test_a_tool_off_for_a_person_is_neither_offered_nor_run_in_a_swarm_worker(
        y_without_posting, monkeypatch) -> None:
    """A real in-process worker in Y's space, its model scripted to call
    ``x_account`` anyway."""
    from unittest.mock import AsyncMock

    import kazma_core.agent.tool_registry as tool_registry
    import kazma_core.model_registry as model_registry
    from kazma_core.llm_provider import ToolCall
    from kazma_core.swarm.task import WorkerCapabilities
    from kazma_core.swarm.worker import InProcessWorker

    registry = _registry()
    entered: list[str] = []

    async def recorder(*_a: Any, **_k: Any) -> str:
        entered.append("x_account")
        return "the account"

    monkeypatch.setitem(registry._tools, "x_account",
                        replace(registry.get_tool("x_account"), func=recorder, is_async=True))
    monkeypatch.setattr(tool_registry, "get_tool_registry", lambda: registry)
    _scripted_model(monkeypatch, [
        _reply(calls=[ToolCall(id="c1", name="x_account", arguments={})]), _reply("done"),
    ])
    provider = model_registry.get_model_registry().get_client()
    offered: list[set[str]] = []
    real_chat = provider.chat.side_effect

    async def recording_chat(*args: Any, **kwargs: Any) -> Any:
        offered.append({t["function"]["name"] for t in kwargs.get("tools") or []})
        return await real_chat(*args, **kwargs)

    provider.chat = AsyncMock(side_effect=recording_chat)
    worker = InProcessWorker(name="probe", role="tester", capabilities=WorkerCapabilities(
        role="tester", tools=["x_account", "x_status"]))
    with y_without_posting.as_a():
        asyncio.run(worker.dispatch("show the account"))
    assert offered, "the worker never called its model"
    assert "x_account" not in offered[0] and "x_status" in offered[0], offered
    assert entered == [], "the worker ran a tool switched off for its person"


def test_control_hiding_the_tool_from_the_offer_only(y_without_posting, monkeypatch) -> None:
    """The plan's control: the offer leaves the tool out, the call never
    asks -- Y's call of x_account runs."""
    m = y_without_posting
    assert "x_account" not in _offered(m, m.as_a())  # hidden ...
    from kazma_core.spaces import tools as host_tools

    real = host_tools.tool_refusal
    monkeypatch.setattr(host_tools, "tool_refusal",
                        lambda name, allowance=None, grants=...: None if name == "x_account"
                        else real(name, allowance, grants))
    _answer, entered = _call(m, m.as_a(), "x_account")
    assert entered == ["x_account"]  # ... and run


# ── People, and the rules of the switch ─────────────────────────────────


def test_people_lists_and_records_the_switch(y_without_posting) -> None:
    m = y_without_posting
    listed = {p["id"]: p for p in people.list_people()}
    x_tools = next(g for g in listed[m.account_a]["tools"] if g["from"] == "x_studio")["tools"]
    assert {t["name"] for t in x_tools if not t["on"]} == OFF
    assert all(t["on"] for g in listed[m.account_b]["tools"] for t in g["tools"])
    events = [e for e in identity.read_registry()["events"] if e.get("kind") == "tools_set"]
    assert events and events[-1]["account"] == m.account_a
    # Switched on again: the whole list is what People sends.
    people.set_tools(m.account_a, [], by=OWNER_ACCOUNT_ID)
    assert "x_post" in _offered(m, m.as_a())


def test_a_feature_that_is_off_lists_no_tools_to_switch(y_without_posting) -> None:
    m = y_without_posting
    people.set_features(m.account_b, {"x_studio": False}, by=OWNER_ACCOUNT_ID)
    listed = {p["id"]: p for p in people.list_people()}
    assert not any(g["from"] == "x_studio" for g in listed[m.account_b]["tools"])


def test_the_switch_refuses_what_is_not_a_persons_tool(y_without_posting) -> None:
    m = y_without_posting
    for off, code in ((["no_such_tool"], "unknown_tool"), (["config_save"], "unknown_tool"),
                      ("x_post", "bad_tools")):
        with pytest.raises(people.PeopleRefused) as refused:
            people.set_tools(m.account_a, off, by=OWNER_ACCOUNT_ID)
        assert refused.value.code == code and refused.value.status == 400, (off, refused.value.code)
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_tools(OWNER_ACCOUNT_ID, ["x_post"], by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "admin_has_every_tool"


def test_without_spaces_nothing_is_refused(y_without_posting, monkeypatch) -> None:
    m = y_without_posting
    monkeypatch.setattr(scope, "_enforced", False)
    _answer, entered = _call(m, m.as_a(), "x_account")
    assert entered == ["x_account"]
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_tools(m.account_a, [], by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "spaces_off"


def test_the_route_sets_the_tools(y_without_posting) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    m = y_without_posting
    app = FastAPI()
    app.include_router(create_people_router())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal",
                      lambda request: {"source": "secret", "actor": "owner", "role": "admin"})
        with TestClient(app) as client:
            got = client.put(f"/api/people/{m.account_a}/tools", json={"off": ["x_post", "x_status"]})
            bad = client.put(f"/api/people/{m.account_a}/tools", json={"off": ["nope"]})
    assert got.status_code == 200 and got.json()["off"] == ["x_post", "x_status"], got.text
    assert bad.status_code == 400 and bad.json()["error"] == "unknown_tool", bad.text
    assert features.grants_of(identity.read_registry(), m.account_a).tools_off == {"x_post", "x_status"}


def test_the_precondition_reads_a_stored_tool_list(y_without_posting) -> None:
    """S1-33's check of the features holds while each stored tool list is a
    list; one that is not is named."""
    m = y_without_posting
    assert features.precondition()[0]

    def spoil(registry: dict[str, Any]) -> None:
        registry["policies"][m.account_a][features.TOOLS_OFF_POLICY] = "x_post"

    identity.update_registry(spoil)
    held, why = features.precondition()
    assert not held and features.TOOLS_OFF_POLICY in why, why
