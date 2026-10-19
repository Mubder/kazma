"""An approval that expires on a chat app's turn is said for what it is, and
the turn's outcome goes back to that chat.

Live 2026-10-18 (Telegram, an X archive):

- 12:03 the card for ``file_apply_patch`` was shown; nobody answered.
- 12:08 the watchdog auto-denied it after 300 s; the model read "Tool
  'file_apply_patch' denied by user" and went on to ask for ``send_file``.
- 12:09 that card went to the web journal only (the watchdog resumes into
  it), so it never reached the chat; it expired unseen at 12:14, again "denied
  by user", and the turn's answer never reached the chat either.
- 12:15 the owner tapped Approve on the first card and was told "Already
  handled -- this approval was applied earlier and the turn finished".
- Later the model told the owner both calls had been refused.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

LIVE_REASON = (
    "HITL approval timed out after 300s. The operator did not answer and is "
    "probably away from the device. This is NOT a refusal of this approach."
)


# ── what the model is told ──────────────────────────────────────────────


class _Exec:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        return {"content": "ok", "is_error": False}


class _Tracer:
    def __getattr__(self, _name):
        return lambda *a, **k: None


async def _answered(monkeypatch, answer: dict[str, Any]) -> dict[str, Any]:
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state

    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: answer, raising=False)
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [{
        "id": "p1", "name": "file_write",
        "arguments": {"path": "notes.md", "content": "x"},
    }]
    executor = _Exec()
    result = await tool_worker_node(
        state, tool_executor=executor, tracer=_Tracer(),
        hitl_config={"enabled": True, "require_approval_for": ["file_write"]},
    )
    assert executor.calls == []
    (done,) = result.get("tool_calls_done") or []
    return done


@pytest.mark.anyio
async def test_an_expired_approval_reaches_the_model_as_a_timeout(monkeypatch):
    done = await _answered(monkeypatch, {
        "approved": False, "timed_out": True, "reason": LIVE_REASON, "scope": "once",
    })
    assert "denied by user" not in done["content"]
    assert "expired unanswered" in done["content"] and "did not decide" in done["content"]
    # The card is gone: the model told the owner to approve it (live 2026-10-20).
    assert "can no longer be approved" in done["content"]
    assert LIVE_REASON in done["content"], "the watchdog's instruction reaches the model"
    assert done.get("outcome") == "terminal", "the round ends: nobody is there to approve more"


@pytest.mark.anyio
async def test_negative_control_a_real_refusal_still_reads_as_one(monkeypatch):
    done = await _answered(monkeypatch, {"approved": False, "reason": "deny", "scope": "once"})
    assert "denied by user" in done["content"]


# ── where the outcome goes ──────────────────────────────────────────────


@pytest.fixture
def watchdog(monkeypatch):
    """The watchdog with its writers replaced; records what it starts."""
    import kazma_core.safety.commitment.resume as resume_mod
    import kazma_ui.delivery as delivery
    import kazma_ui.hitl_decision as hd
    import kazma_ui.hitl_status as hs
    import kazma_ui.hitl_timeout as wd
    import kazma_ui.reply_sink as reply_sink
    import kazma_ui.sse_chat._streaming as streaming
    import kazma_ui.turn_runtime as tr_mod

    seen: dict[str, Any] = {"journal": [], "chat": []}

    async def _pending(graph, cfg):
        return {"tool": "send_file", "kind": "security", "interrupt_id": "g1"}, "g1"

    async def _record(*a, **k):
        return "g1"

    class _Broker:
        async def emit(self, thread_id, frame):
            return frame

    async def _journal(*a, **k):
        seen["journal"].append(k.get("thread_id"))

    async def _chat(**k):
        seen["chat"].append(k)

    monkeypatch.setattr(resume_mod, "pending_interrupt", _pending)
    monkeypatch.setattr(resume_mod, "build_resume_command", lambda *a, **k: "deny-cmd")
    monkeypatch.setattr(hs, "is_resume_claimed", lambda t: False)
    monkeypatch.setattr(hd, "record_gate_decision", _record)
    monkeypatch.setattr(delivery, "get_turn_broker", lambda: _Broker())
    monkeypatch.setattr(tr_mod, "resolve_session_id", lambda t: "sess")
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a, **k: "turn")
    monkeypatch.setattr(streaming, "_drive_graph_to_journal", _journal)
    monkeypatch.setattr(streaming, "mark_thread_unpaused", lambda t: None)
    monkeypatch.setattr(wd, "_chat_app_resumer", None)
    wd.register_chat_app_resumer(frozenset({"telegram", "discord", "slack"}), _chat)
    return wd, seen


def _graph(routing: dict[str, str] | None):
    async def aget_state(config):
        return SimpleNamespace(values={"_gateway": routing} if routing else {}, next=("tools",))

    return SimpleNamespace(aget_state=aget_state)


async def _run(wd) -> None:
    await asyncio.gather(*list(wd._deny_tasks))


@pytest.mark.anyio
async def test_a_chat_apps_expired_approval_is_finished_in_its_chat(watchdog):
    wd, seen = watchdog
    graph = _graph({"platform": "telegram", "delivery_target": "telegram:1804015016",
                    "thread_id": "t1"})
    await wd._auto_deny(graph, "t1", 300.0)
    await _run(wd)
    assert seen["journal"] == []
    (call,) = seen["chat"]
    assert call["platform"] == "telegram" and call["target_id"] == "telegram:1804015016"
    assert call["tool"] == "send_file" and call["resume_cmd"] == "deny-cmd"


@pytest.mark.anyio
async def test_negative_control_a_web_turn_still_resumes_into_the_journal(watchdog):
    wd, seen = watchdog
    graph = _graph({"platform": "web", "delivery_target": "telegram:1804015016"})
    await wd._auto_deny(graph, "t-web", 300.0)
    await _run(wd)
    assert seen["chat"] == [] and seen["journal"] == ["t-web"]


@pytest.mark.anyio
async def test_a_chat_turn_with_no_address_falls_back_to_the_journal(watchdog):
    wd, seen = watchdog
    await wd._auto_deny(_graph({"platform": "telegram", "delivery_target": "telegram:unknown"}),
                        "t-x", 300.0)
    await _run(wd)
    assert seen["chat"] == [] and seen["journal"] == ["t-x"]


# ── what the chat receives ──────────────────────────────────────────────


class _Manager:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, outbound) -> None:
        self.sent.append(outbound)


@pytest.mark.anyio
async def test_the_chat_gets_the_notice_then_the_answer(monkeypatch):
    from kazma_gateway.agent_handler import hitl

    async def _resumed(graph, cmd, config, thread_id):
        answer = "I could not send the file: the approval expired. Approve it when you are back."
        return {"messages": [{"role": "assistant", "content": answer}]}, None

    monkeypatch.setattr(hitl, "_run_resume", _resumed)
    manager = _Manager()
    resume = hitl.make_timeout_resumer(object(), manager, None)
    await resume(thread_id="t1", resume_cmd="deny-cmd", resume_config={}, platform="telegram",
                 target_id="telegram:1804015016", tool="send_file", timeout_s=300.0)
    assert [m.target_id for m in manager.sent] == ["telegram:1804015016"] * 2
    assert "send_file" in manager.sent[0].text and "5 minutes" in manager.sent[0].text
    assert "approval expired" in manager.sent[1].text
    assert manager.sent[1].context_metadata.get("parse_mode") == "HTML"


@pytest.mark.anyio
async def test_a_card_the_resumed_turn_pauses_on_reaches_the_chat(monkeypatch):
    from kazma_gateway.agent_handler import hitl

    card = {"type": "hitl_approval", "kind": "security", "tool": "send_file",
            "args": {"file_path": "a.pdf"}, "interrupt_id": "g2"}

    async def _resumed(graph, cmd, config, thread_id):
        return {"messages": []}, card

    monkeypatch.setattr(hitl, "_run_resume", _resumed)
    manager = _Manager()
    await hitl.make_timeout_resumer(object(), manager, None)(
        thread_id="t1", resume_cmd="c", resume_config={}, platform="telegram",
        target_id="telegram:7", tool="file_apply_patch", timeout_s=300.0)
    assert len(manager.sent) == 2
    assert "send_file" in manager.sent[1].text
    assert manager.sent[1].context_metadata.get("reply_markup"), "the card has its buttons"


# ── the tap on the old card ─────────────────────────────────────────────


@pytest.fixture
def gates(tmp_path):
    import kazma_core.safety.hitl_gates as hg

    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    yield hg
    hg.set_db_path_for_tests(None)


async def _late_tap(gates, *, decision: str, actor: str, requested_gate: str) -> str | None:
    from kazma_gateway.agent_handler.hitl import _stale_approval_message

    gates.register_gate(gates.GateRow(gate_id="g9", thread_id="t9", tool="file_apply_patch"))
    gates.claim_gate("g9", decision, actor)
    gates.settle_gate("g9")
    finished = SimpleNamespace(next=(), values={"messages": [
        {"role": "user", "content": "start again"},
        {"role": "assistant", "content": "Sent."},
    ]})

    async def aget_state(config):
        return finished

    return await _stale_approval_message(
        SimpleNamespace(aget_state=aget_state), {}, "t9-" + decision,
        action="approve", approved=True, requested_gate=requested_gate,
    )


@pytest.mark.anyio
async def test_a_tap_on_an_expired_card_says_it_expired(gates):
    text = await _late_tap(gates, decision="deny", actor="watchdog:timeout", requested_gate="g9")
    assert text and "expired" in text and "file_apply_patch was not run" in text
    assert "Already handled" not in text


@pytest.mark.anyio
async def test_a_tap_on_a_denied_card_says_it_was_denied(gates):
    text = await _late_tap(gates, decision="deny", actor="telegram:1", requested_gate="g9")
    assert text and "denied" in text and "Already handled" not in text


@pytest.mark.anyio
async def test_negative_control_without_the_cards_record_the_old_message_returns(gates):
    """The live answer, from judging by the finished turn alone."""
    text = await _late_tap(gates, decision="deny", actor="watchdog:timeout", requested_gate="")
    assert text and "Already handled" in text
