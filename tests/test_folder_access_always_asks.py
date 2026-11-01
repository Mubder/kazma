"""A folder-access request asks every time, even under YOLO or a grant.

Live 2026-10-26 a chat with YOLO on ran ``request_path_access`` with no card;
the daily digest's "ran without asking" found it on 2026-11-01. A YOLO window
skips every danger tool, and a folder-access request widens what every file
tool may read or write for the session. The danger list's comment already said
"Path grants expand the FS allowlist -- always require human approval", but
the list YOLO and grants cannot skip (``ALWAYS_HITL_TOOLS``) held the X writes
only. The owner's decision that day: it always asks. Its card takes a one-time
approval only, keeps the ordinary header (nothing is published) and says it
asks every time.
"""

from __future__ import annotations

import pytest
from kazma_core.safety import hitl
from kazma_core.safety.hitl import (
    ALWAYS_HITL_TOOLS,
    approval_need,
    reset_current_thread_id,
    set_current_thread_id,
)
from kazma_core.safety.hitl_grants import clear_grants, grant_tool, one_time_only
from kazma_core.safety.task_grants import clear_task_grant, grant_task
from kazma_core.safety.unasked import BY_TASK_GRANT, BY_YOLO
from kazma_core.safety.yolo import disable_yolo, enable_yolo

TOOL = "request_path_access"
TID = "thr-folder-access"
HITL_ON = {"enabled": True}
#: The list before 2026-11-01: the negative control.
OLD_LIST = frozenset({"x_post", "x_delete_post", "x_schedule_post", "x_cancel_scheduled_post"})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.delenv("KAZMA_ALLOW_YOLO", raising=False)
    disable_yolo(TID)
    clear_grants(TID)
    clear_task_grant(TID)
    token = set_current_thread_id(TID)
    yield
    reset_current_thread_id(token)
    disable_yolo(TID)
    clear_grants(TID)
    clear_task_grant(TID)


def _old_list(monkeypatch):
    monkeypatch.setattr(hitl, "ALWAYS_HITL_TOOLS", OLD_LIST)


def _standing(kind: str) -> None:
    if kind == "yolo":
        enable_yolo(TID, actor="web:admin:test")
    else:
        grant_task(TID, actor="web:admin:test")


@pytest.mark.parametrize(("kind", "by"), [("yolo", BY_YOLO), ("task", BY_TASK_GRANT)])
def test_it_asks_under_each_standing_approval(kind, by):
    _standing(kind)
    assert approval_need(TOOL, HITL_ON) == (True, None)
    assert approval_need("file_write", HITL_ON) == (False, by)  # the approval is on


@pytest.mark.parametrize(("kind", "by"), [("yolo", BY_YOLO), ("task", BY_TASK_GRANT)])
def test_with_the_old_list_it_ran_unasked(kind, by, monkeypatch):
    """Negative control: the 2026-10-26 shape."""
    _old_list(monkeypatch)
    _standing(kind)
    assert approval_need(TOOL, HITL_ON) == (False, by)


def test_its_card_takes_a_one_time_approval_only(monkeypatch):
    assert one_time_only([{"name": "file_write"}, {"name": TOOL}]) == [TOOL]
    refused = grant_tool(TID, TOOL, actor="web:admin:test")
    assert refused["active"] is False and refused.get("error") == "one_time_only"
    assert approval_need(TOOL, HITL_ON) == (True, None)
    _old_list(monkeypatch)
    assert grant_tool(TID, TOOL, actor="web:admin:test")["active"] is True  # control


def _bus_allows() -> bool:
    import asyncio

    from kazma_core.swarm.safety import SafetyMiddleware

    return asyncio.run(SafetyMiddleware(allow_headless_danger=False).check(TOOL))


def test_the_swarm_bus_asks_under_yolo(monkeypatch):
    enable_yolo(TID, actor="web:admin:test")
    assert _bus_allows() is False  # no bus adapter: asked, so refused here
    _old_list(monkeypatch)
    assert _bus_allows() is True  # control: YOLO let it through


def test_the_swarm_fallback_is_the_same_list():
    from kazma_core.swarm.safety import _ALWAYS_HITL_FALLBACK

    assert _ALWAYS_HITL_FALLBACK == ALWAYS_HITL_TOOLS


class _AskedError(Exception):
    pass


class _Exec:
    def __init__(self):
        self.ran: list[str] = []

    async def execute(self, name, args):
        self.ran.append(name)
        return {"content": "ok", "is_error": False}


class _Tracer:
    def trace_tool_execution(self, **kwargs):
        pass


ARGS = {
    TOOL: {"path": "C:/Users/someone/Documents", "mode": "read", "scope": "session"},
    "x_delete_post": {"post_id": "1850000000000000000"},
}


def _state(tool: str = TOOL) -> dict:
    from kazma_core.agent.state import PendingToolCall

    return {
        "thread_id": TID,
        "messages": [{"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function", "function": {"name": tool, "arguments": "{}"}}]}],
        "tool_calls_pending": [PendingToolCall(id="c1", name=tool, arguments=ARGS[tool])],
        "tool_calls_done": [],
        "tool_results": {},
        "consecutive_tool_failures": 0,
    }


async def _run_worker(monkeypatch, tool: str = TOOL) -> tuple[list[dict], list[str]]:
    import langgraph.types as lg_types
    from kazma_core.agent.graph_tool_worker import tool_worker_node

    cards: list[dict] = []

    def interrupt(payload):
        cards.append(payload)
        raise _AskedError

    monkeypatch.setattr(lg_types, "interrupt", interrupt)
    executor = _Exec()
    try:
        await tool_worker_node(_state(tool), tool_executor=executor, tracer=_Tracer(),
                               hitl_config=HITL_ON)
    except _AskedError:
        pass
    return cards, executor.ran


@pytest.mark.asyncio
async def test_the_chat_graph_shows_its_card_under_yolo(monkeypatch):
    enable_yolo(TID, actor="web:admin:test")
    cards, ran = await _run_worker(monkeypatch)
    assert ran == []
    assert len(cards) == 1
    assert cards[0]["always_ask"] is True and cards[0]["yolo_allowed"] is False


@pytest.mark.asyncio
async def test_with_the_old_list_the_chat_graph_ran_it(monkeypatch):
    """Negative control: no card, the call ran."""
    _old_list(monkeypatch)
    enable_yolo(TID, actor="web:admin:test")
    cards, ran = await _run_worker(monkeypatch)
    assert cards == [] and ran == [TOOL]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [TOOL, "x_delete_post"])
async def test_a_yolo_window_is_not_a_gate_that_is_off(tool, monkeypatch):
    """Under YOLO the live config says the gate is off for the thread, and the
    chat refused every always-ask tool as if approvals were off ("Re-enable
    safety.hitl.enabled"), X posts too. The config now says it is a YOLO
    window (``yolo``), and the card is shown."""
    import kazma_core.safety.hitl as hitl_mod

    enable_yolo(TID, actor="web:admin:test")
    config = hitl_mod.get_hitl_config({})
    assert config["enabled"] is False and config["yolo"] is True
    cards, ran = await _run_worker(monkeypatch, tool)
    assert len(cards) == 1 and ran == []

    real = hitl_mod.get_hitl_config  # control: the config without the flag
    monkeypatch.setattr(hitl_mod, "get_hitl_config",
                        lambda raw=None: {**real(raw), "yolo": False})
    cards, ran = await _run_worker(monkeypatch, tool)
    assert cards == [] and ran == []


@pytest.mark.asyncio
async def test_a_gate_the_operator_turned_off_still_refuses_it(monkeypatch):
    """With approvals off nothing can ask: the call is refused, YOLO or not."""
    import kazma_core.safety.hitl as hitl_mod

    real = hitl_mod.get_hitl_config
    enable_yolo(TID, actor="web:admin:test")
    monkeypatch.setattr(hitl_mod, "get_hitl_config",
                        lambda raw=None: {**real(raw), "enabled": False, "yolo": False})
    cards, ran = await _run_worker(monkeypatch)
    assert cards == [] and ran == []


def _telegram_card(tool: str) -> tuple[str, list[str]]:
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    payload = {"tool": tool, "args": {"path": "C:/Users/someone/Documents"},
               "tools": [{"name": tool, "args": {}}], "interrupt_id": "gate-1"}
    out = _build_approval_prompt(payload, "th-1", platform="telegram")
    return out["text"], [b["text"] for row in out["markup"]["inline_keyboard"] for b in row]


def test_a_chat_app_card_says_it_asks_every_time():
    from kazma_gateway.agent_handler.hitl import ASKS_EVERY_TIME

    text, buttons = _telegram_card(TOOL)
    assert text.startswith("⚠️ Approval required")  # nothing is published
    assert ASKS_EVERY_TIME in text
    assert not any("task" in b.lower() for b in buttons), buttons
    other, other_buttons = _telegram_card("file_write")  # control
    assert ASKS_EVERY_TIME not in other
    assert any("task" in b.lower() for b in other_buttons), other_buttons


def test_a_chat_app_card_for_several_tools_says_it_too():
    from kazma_gateway.agent_handler.hitl import ASKS_EVERY_TIME, _build_approval_prompt

    payload = {"tool": "2 tools", "args": {}, "interrupt_id": "gate-1",
               "tools": [{"name": "file_write", "args": {}}, {"name": TOOL, "args": {}}]}
    assert ASKS_EVERY_TIME in _build_approval_prompt(payload, "th-1", platform="telegram")["text"]
