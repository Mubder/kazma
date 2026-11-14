"""A2A, agent to agent (REMAINING 4.5): the agent card, and A2A's JSON-RPC
calls (message/send, tasks/get, tasks/cancel) on the real router. The turn is
the scheduled task's read-only graph (checked by name) run by a stand-in, so
nothing here calls a model.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.a2a_protocol import JSONRPC_ERRORS, A2AError, agent_card, read_send_params
from kazma_ui import a2a_api


@pytest.fixture
def a2a(monkeypatch):
    calls: dict[str, Any] = {"graphs": [], "states": [], "persist": [], "release": None, "answer": "The answer."}
    monkeypatch.setattr(a2a_api, "_enabled", lambda: True)
    monkeypatch.setattr(a2a_api, "_STORE", a2a_api._Store())

    def fake_graph(agent, *, capabilities):
        calls["graphs"].append(capabilities)
        return object()

    async def fake_turn(*, graph, thread_id, state, config, persist):
        calls["states"].append(state)
        calls["persist"].append(persist)
        if calls["release"] is not None:
            await calls["release"].wait()
        if calls["answer"] is None:
            return SimpleNamespace(text="", error="boom", turn_failed=True, interrupted=False)
        return SimpleNamespace(text=calls["answer"], error=None, turn_failed=False, interrupted=False)

    monkeypatch.setattr("kazma_core.cron.capabilities.cron_fire_graph", fake_graph)
    monkeypatch.setattr("kazma_core.agent.turn.run_agent_turn", fake_turn)
    app = FastAPI()
    app.include_router(a2a_api.create_a2a_router(lambda: object()))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, calls=calls)


def _send(client, text="What is in my notes about tiles?", **extra):
    message = {"role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": text}]}
    message.update(extra.pop("message", {}))
    body = {"jsonrpc": "2.0", "id": 7, "method": "message/send", "params": {"message": message, **extra}}
    return client.post("/api/a2a", json=body).json()


def test_the_card_names_kazma_and_its_endpoint(a2a) -> None:
    card = a2a.client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "Kazma" and card["url"].endswith("/api/a2a")
    assert card["protocolVersion"] == "0.3.0" and card["skills"][0]["id"] == "ask"
    assert card["security"] == [{"bearer": []}] and card["capabilities"]["streaming"] is False
    assert agent_card(url="https://x/api/a2a", version="1")["url"] == "https://x/api/a2a"


def test_off_by_default_answers_nothing(a2a, monkeypatch) -> None:
    monkeypatch.setattr(a2a_api, "_enabled", lambda: False)
    assert a2a.client.get("/.well-known/agent-card.json").status_code == 404
    assert a2a.client.post("/api/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get"}).status_code == 404


def test_a_question_is_one_read_only_turn_answered_as_a_task(a2a) -> None:
    out = _send(a2a.client)
    task = out["result"]
    assert out["id"] == 7 and task["kind"] == "task" and task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"] == [{"kind": "text", "text": "The answer."}]
    assert a2a.calls["graphs"] == ["read"], "the scheduled task's read-only graph"
    assert a2a.calls["persist"] == [False], "nothing is written to memory"
    messages = a2a.calls["states"][0]["messages"]
    assert messages[0]["role"] == "system" and "another AI agent" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "What is in my notes about tiles?"}
    got = a2a.client.post("/api/a2a", json={"jsonrpc": "2.0", "id": 8, "method": "tasks/get",
                                            "params": {"id": task["id"]}}).json()
    assert got["result"]["status"]["state"] == "completed"


def test_a_conversation_keeps_its_last_turns(a2a) -> None:
    first = _send(a2a.client, "First question")["result"]
    _send(a2a.client, "And then?", message={"contextId": first["contextId"]})
    messages = a2a.calls["states"][1]["messages"]
    assert [m["content"] for m in messages[1:3]] == ["First question", "The answer."]


def test_not_blocking_answers_working_then_the_task_finishes(a2a) -> None:
    a2a.calls["release"] = asyncio.Event()
    task = _send(a2a.client, configuration={"blocking": False})["result"]
    assert task["status"]["state"] == "working"
    a2a_api._STORE.tasks[task["id"]].handle.get_loop().call_soon_threadsafe(a2a.calls["release"].set)
    deadline = time.monotonic() + 10
    state = "working"
    while state == "working" and time.monotonic() < deadline:
        state = a2a.client.post("/api/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                                                  "params": {"id": task["id"]}}).json()["result"]["status"]["state"]
        time.sleep(0.05)
    assert state == "completed"


def test_cancel_ends_a_running_task(a2a) -> None:
    a2a.calls["release"] = asyncio.Event()  # never set: the turn waits
    task = _send(a2a.client, configuration={"blocking": False})["result"]
    out = a2a.client.post("/api/a2a", json={"jsonrpc": "2.0", "id": 3, "method": "tasks/cancel",
                                            "params": {"id": task["id"]}}).json()
    assert out["result"]["status"]["state"] == "canceled"
    again = a2a.client.post("/api/a2a", json={"jsonrpc": "2.0", "id": 4, "method": "tasks/cancel",
                                              "params": {"id": task["id"]}}).json()
    assert again["error"]["code"] == JSONRPC_ERRORS["task_not_cancelable"]


def test_a_failed_turn_is_a_failed_task(a2a) -> None:
    a2a.calls["answer"] = None
    task = _send(a2a.client)["result"]
    assert task["status"]["state"] == "failed" and "could not answer" in task["status"]["message"]["parts"][0]["text"]
    assert "artifacts" not in task


def test_what_kazma_refuses_is_a_jsonrpc_error(a2a) -> None:
    c = a2a.client
    assert c.post("/api/a2a", content=b"{nope", headers={"content-type": "application/json"}).json()["error"]["code"] \
        == JSONRPC_ERRORS["parse_error"]
    assert c.post("/api/a2a", json={"id": 1, "method": "x"}).json()["error"]["code"] == JSONRPC_ERRORS["invalid_request"]
    assert c.post("/api/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/resubscribe"}).json()["error"]["code"] \
        == JSONRPC_ERRORS["method_not_found"]
    image = _send(c, message={"parts": [{"kind": "file", "file": {"uri": "https://x"}}]})
    assert image["error"]["code"] == JSONRPC_ERRORS["content_type_not_supported"]
    assert c.post("/api/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "nope"}}) \
        .json()["error"]["code"] == JSONRPC_ERRORS["task_not_found"]


def test_more_than_two_at_once_is_busy(a2a) -> None:
    a2a.calls["release"] = asyncio.Event()
    for _ in range(a2a_api.MAX_RUNNING):
        assert _send(a2a.client, configuration={"blocking": False})["result"]["status"]["state"] == "working"
    busy = _send(a2a.client, configuration={"blocking": False})
    assert busy["error"]["code"] == JSONRPC_ERRORS["busy"]


@pytest.mark.parametrize("params, code", [({}, "invalid_params"),
                                          ({"message": {"role": "agent", "parts": [{"kind": "text", "text": "x"}]}},
                                           "invalid_params"),
                                          ({"message": {"parts": [{"kind": "text", "text": "  "}]}}, "invalid_params"),
                                          ({"message": {"parts": [{"kind": "data", "data": {}}]}},
                                           "content_type_not_supported")])
def test_reading_a_request(params, code) -> None:
    with pytest.raises(A2AError) as refused:
        read_send_params(params)
    assert refused.value.code == code
    text, context, _ = read_send_params({"message": {"parts": [{"kind": "text", "text": "hi"}], "contextId": "c1"}})
    assert (text, context) == ("hi", "c1")


def test_the_endpoint_needs_sign_in_and_the_card_does_not() -> None:
    from kazma_core.security.platform_rbac import role_allows
    from kazma_ui.auth import is_sensitive_path

    assert is_sensitive_path("/api/a2a") and not is_sensitive_path("/.well-known/agent-card.json")
    assert not role_allows("operator", "/api/settings/single", "PUT"), "switching it on is an administrator's"
