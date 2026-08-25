"""First-class plan mode — structural read-only, then execute on approve."""

from __future__ import annotations

import pytest

from kazma_core.agent.plan_mode import (
    PLAN_EXECUTE_MARKER,
    PLAN_MODE_MARKER,
    apply_plan_command,
    apply_plan_mode_constraints,
    apply_plan_mode_to_turn,
    disable_plan_mode,
    enable_plan_mode,
    is_plan_approve_reply,
    is_plan_command,
    is_plan_mode,
    plan_mode_enabled,
)
from kazma_core.agent.turn_input import (
    AUDIT_ONLY_ALLOWLIST,
    filter_tools_for_constraints,
    is_tool_allowed_under_constraints,
)


def _tid(name: str) -> str:
    return f"plan-test-{name}"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("KAZMA_PLAN_MODE", raising=False)
    for name in ("on", "off", "go", "task", "turn", "kill"):
        disable_plan_mode(_tid(name))
    yield
    for name in ("on", "off", "go", "task", "turn", "kill"):
        disable_plan_mode(_tid(name))


class TestCommands:
    def test_is_plan_command(self) -> None:
        assert is_plan_command("/plan on", require_slash=True) is True
        assert is_plan_command("plan on", require_slash=True) is False
        assert is_plan_command("plan on", require_slash=False) is True
        assert is_plan_command("hello", require_slash=False) is False

    def test_on_off_status(self) -> None:
        tid = _tid("on")
        r = apply_plan_command(tid, "/plan on", actor="t")
        assert r.handled is True
        assert r.plan_active is True
        assert r.rewrite_user_text is None
        assert is_plan_mode(tid) is True
        st = apply_plan_command(tid, "/plan status", actor="t")
        assert "ON" in st.reply
        off = apply_plan_command(tid, "/plan off", actor="t")
        assert off.plan_active is False
        assert is_plan_mode(tid) is False

    def test_plan_task_rewrites(self) -> None:
        tid = _tid("task")
        r = apply_plan_command(tid, "/plan add oauth to the API", actor="t")
        assert r.action == "plan_task"
        assert r.rewrite_user_text == "add oauth to the API"
        assert is_plan_mode(tid) is True

    def test_plan_go_rewrites_execute(self) -> None:
        tid = _tid("go")
        apply_plan_command(tid, "/plan on", actor="t")
        r = apply_plan_command(tid, "/plan go", actor="t")
        assert r.action == "execute"
        assert r.rewrite_user_text is not None
        assert "approved" in r.rewrite_user_text.lower()
        assert is_plan_mode(tid) is False

    def test_plan_go_without_plan(self) -> None:
        tid = _tid("go")
        r = apply_plan_command(tid, "/plan go", actor="t")
        assert r.rewrite_user_text is None
        assert "No plan" in r.reply

    def test_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_PLAN_MODE", "0")
        assert plan_mode_enabled() is False
        tid = _tid("kill")
        r = apply_plan_command(tid, "/plan on", actor="t")
        assert r.action == "disabled"
        assert is_plan_mode(tid) is False


class TestApproveReply:
    def test_proceed_and_approve(self) -> None:
        assert is_plan_approve_reply("Proceed") is True
        assert is_plan_approve_reply("approve the plan") is True
        assert is_plan_approve_reply("execute") is True
        assert is_plan_approve_reply("") is False
        assert is_plan_approve_reply("please also add tests for the login form") is False


class TestConstraints:
    def test_unions_read_only(self) -> None:
        out = apply_plan_mode_constraints(["no_send"])
        assert "no_send" in out
        assert "read_only" in out
        assert "no_writes" in out

    def test_allowlist_has_codebase_search(self) -> None:
        assert "codebase_search" in AUDIT_ONLY_ALLOWLIST
        assert "codebase_status" in AUDIT_ONLY_ALLOWLIST

    def test_filter_blocks_writes(self) -> None:
        cons = apply_plan_mode_constraints([])
        assert is_tool_allowed_under_constraints("file_read", cons) is True
        assert is_tool_allowed_under_constraints("codebase_search", cons) is True
        assert is_tool_allowed_under_constraints("file_write", cons) is False
        assert is_tool_allowed_under_constraints("file_apply_patch", cons) is False
        assert is_tool_allowed_under_constraints("shell_exec", cons) is False
        defs = [
            {"type": "function", "function": {"name": "file_read", "parameters": {}}},
            {"type": "function", "function": {"name": "file_write", "parameters": {}}},
            {"type": "function", "function": {"name": "codebase_search", "parameters": {}}},
        ]
        kept = [d["function"]["name"] for d in filter_tools_for_constraints(defs, cons)]
        assert kept == ["file_read", "codebase_search"]


class TestTurn:
    def test_injects_plan_note(self) -> None:
        tid = _tid("turn")
        enable_plan_mode(tid, actor="t")
        cons, msgs, kind = apply_plan_mode_to_turn(
            tid,
            hard_constraints=[],
            messages=[{"role": "user", "content": "add auth"}],
            user_text="add auth",
        )
        assert kind == "plan"
        assert "read_only" in cons
        assert any(PLAN_MODE_MARKER in str(m.get("content")) for m in msgs)

    def test_proceed_exits_to_execute(self) -> None:
        tid = _tid("turn")
        enable_plan_mode(tid, actor="t")
        cons, msgs, kind = apply_plan_mode_to_turn(
            tid,
            hard_constraints=["read_only"],
            messages=[{"role": "user", "content": "Proceed"}],
            user_text="Proceed",
        )
        assert kind == "execute"
        assert is_plan_mode(tid) is False
        assert any(PLAN_EXECUTE_MARKER in str(m.get("content")) for m in msgs)
        assert "read_only" not in cons
        assert "no_writes" not in cons

    def test_pending_execute_from_plan_go(self) -> None:
        tid = _tid("go")
        apply_plan_command(tid, "/plan on", actor="t")
        apply_plan_command(tid, "/plan go", actor="t")
        cons, msgs, kind = apply_plan_mode_to_turn(
            tid,
            hard_constraints=[],
            messages=[{"role": "user", "content": "Execute the approved plan."}],
            user_text="Execute the approved plan.",
        )
        assert kind == "execute"
        assert any(PLAN_EXECUTE_MARKER in str(m.get("content")) for m in msgs)
        assert is_plan_mode(tid) is False
        # second hop is off
        _, _, kind2 = apply_plan_mode_to_turn(
            tid, hard_constraints=[], messages=msgs, user_text="next"
        )
        assert kind2 == "off"
