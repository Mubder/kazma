"""A reply names the model that answered after a reload, and the chat goes
on with the model it ran with.

FA-210 made the turn's own save (the graph pump) and its ``done`` frame name
the model that ANSWERED. The browser's stream then saved the reply once more
when it saw ``done``, with the model the page had ASKED for, over the pump's:
live the bubble said "harness", reloaded "harness-model"
(tests/e2e/test_plan_fence_reload.py, failing since FA-210).

The two are different facts and the row keeps both: ``model`` (who answered,
shown on the reply) and ``run_model`` (what the turn ran with). A client that
names no model continues with ``run_model``: after a failover ``model`` is the
backup, and the name a provider gives its answer need not be one Kazma can
call.

Through the real chat route, the graph replaced by the journal frames the
pump writes. The controls: a ``done`` that names no model keeps the asked
model (the test can tell the two apart), and a row stored before
``run_model`` is continued with its ``model``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

ASKED = "asked-model"
ANSWERED = "answered-model"


@pytest.fixture(autouse=True)
def _fresh_sessions():
    """Fresh chats, and the test's models on a loopback provider with a key:
    the route refuses a pinned model it cannot call before it streams."""
    from kazma_core.config_store import get_config_store
    from kazma_core.model_registry import reset_model_registry
    from kazma_ui.session_manager import reset_session_manager

    reset_session_manager()
    store = get_config_store()
    store.set("providers.list", [{
        "name": "custom", "base_url": "http://127.0.0.1:1/v1", "api_key": "sk-answering-model-test",
        "models": [ASKED, ANSWERED, "older-model"], "enabled": True,
    }], category="providers")
    store.batch_set([
        ("registry.active_provider", "custom", "registry"),
        ("registry.active_model", ASKED, "registry"),
    ])
    reset_model_registry()
    yield
    reset_session_manager()
    reset_model_registry()


def _turn(monkeypatch, sid: str, *, body_model: str | None, done_model: str | None,
          pins: list[str]) -> dict[str, Any]:
    """One turn through the route; the reply row it stored."""
    from kazma_ui import sse_chat
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.sse_chat import _sse_frame, create_sse_chat_router
    from kazma_ui.turn_document import parts_from_stream
    from kazma_ui.turn_runtime import persist_reply

    # Past 50 characters: the stream saves the row as it arrives, and
    # the pump's save below finds it.
    final = "The answer, long enough for the stream to save it as it arrives."

    async def drive(*args, **kwargs):
        pass

    async def attach(thread_id, session_id, *args, **kwargs):
        yield _sse_frame("token", {"content": final})
        # The pump saves first, naming the model that answered (FA-210)...
        row = get_session_manager().get(session_id).messages[-1]
        assert persist_reply(session_id, row["turn_id"], final, thread_id=thread_id,
                             model=done_model or "", parts=parts_from_stream(final=final))
        # ...then tells the page the turn is over.
        done = {"content": final, "interrupted": False, "tokens": 12, "cost": 0.001}
        if done_model:
            done["model"] = done_model
        yield _sse_frame("done", done)

    import kazma_core.runtime.turn_model as turn_model

    real_pin = turn_model.pin_turn_model

    def pin(model):
        pins.append(model)
        return real_pin(model)

    monkeypatch.setattr(turn_model, "pin_turn_model", pin)
    monkeypatch.setattr(sse_chat, "_drive_graph_to_journal", drive)
    monkeypatch.setattr(sse_chat, "_sse_attach_stream", attach)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    router = create_sse_chat_router(graph=graph, system_prompt="Test identity.")
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/chat/stream")
    payload: dict[str, Any] = {"message": "Say something short.", "session_id": sid}
    if body_model is not None:
        payload["model"] = body_model

    async def receive():
        return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}

    async def run() -> None:
        response = await endpoint(Request({"type": "http", "method": "POST", "headers": []}, receive))
        async for _frame in response.body_iterator:
            pass

    asyncio.run(run())
    rows = get_session_manager().get(sid).messages
    return [r for r in rows if r.get("role") == "assistant"][-1]


def test_the_reply_keeps_the_answering_model_and_the_model_it_ran_with(monkeypatch):
    row = _turn(monkeypatch, "answering-model", body_model=ASKED, done_model=ANSWERED, pins=[])
    assert row["model"] == ANSWERED, row  # the old save wrote ASKED over it
    assert row["run_model"] == ASKED, row
    assert row["content"].startswith("The answer, long enough")


def test_control_a_done_that_names_no_model_keeps_the_asked_one(monkeypatch):
    row = _turn(monkeypatch, "no-done-model", body_model=ASKED, done_model=None, pins=[])
    assert row["model"] == ASKED and row["run_model"] == ASKED, row


def test_a_client_naming_no_model_continues_with_the_one_the_chat_ran_with(monkeypatch):
    _turn(monkeypatch, "continues", body_model=ASKED, done_model=ANSWERED, pins=[])
    pins: list[str] = []
    _turn(monkeypatch, "continues", body_model=None, done_model=ANSWERED, pins=pins)
    # Continued with the answering model, a chat that failed over once would
    # stay on the backup for good.
    assert pins and set(pins) == {ASKED}, pins


def test_control_a_row_stored_before_run_model_is_continued_with_its_model(monkeypatch):
    from kazma_ui.session_manager import get_session_manager

    sid = "older-row"
    _turn(monkeypatch, sid, body_model=ASKED, done_model=ASKED, pins=[])
    session = get_session_manager().get(sid)
    for row in session.messages:
        row.pop("run_model", None)
        if row.get("role") == "assistant":
            row["model"] = "older-model"
    pins: list[str] = []
    _turn(monkeypatch, sid, body_model=None, done_model=ASKED, pins=pins)
    assert pins and set(pins) == {"older-model"}, pins
