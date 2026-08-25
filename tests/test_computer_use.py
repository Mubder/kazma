"""Computer-use loop (no Playwright / live LLM)."""

from __future__ import annotations

import pytest

from kazma_core.tools.computer_use import (
    HARD_CAP,
    computer_use,
    computer_use_enabled,
    parse_action,
    plan_next_action,
)


class TestParseAction:
    def test_json_object(self) -> None:
        a = parse_action('{"action":"click","x":10,"y":20}')
        assert a.action == "click"
        assert a.x == 10
        assert a.y == 20

    def test_fenced(self) -> None:
        a = parse_action("```json\n{\"action\":\"done\",\"reason\":\"ok\"}\n```")
        assert a.action == "done"

    def test_unknown_fails(self) -> None:
        a = parse_action({"action": "explode"})
        assert a.action == "fail"


class TestKillSwitch:
    def test_enabled_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAZMA_COMPUTER_USE", raising=False)
        assert computer_use_enabled() is True

    def test_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_COMPUTER_USE", "0")
        assert computer_use_enabled() is False

    @pytest.mark.asyncio
    async def test_tool_respects_kill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_COMPUTER_USE", "0")
        out = await computer_use("open settings")
        assert "disabled" in out.lower()

    @pytest.mark.asyncio
    async def test_empty_goal(self) -> None:
        out = await computer_use("  ")
        assert "goal" in out.lower()


class TestPlan:
    @pytest.mark.asyncio
    async def test_injectable_chat(self) -> None:
        async def fake(_messages):
            return {"action": "done", "reason": "already there"}

        a = await plan_next_action("x", "aaaa", [], chat_fn=fake)
        assert a.action == "done"


def test_hard_cap() -> None:
    assert HARD_CAP >= 8


class TestNativePlanners:
    def test_kill_switch_is_vision_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_core.tools.computer_use_planners import resolve_planner_kind

        monkeypatch.setenv("KAZMA_CUA_PLANNER", "0")
        assert resolve_planner_kind("claude-sonnet-4") == "vision_json"

    def test_claude_selects_cua(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_core.tools.computer_use_planners import resolve_planner_kind

        monkeypatch.setenv("KAZMA_CUA_PLANNER", "1")
        assert resolve_planner_kind("claude-sonnet-4") == "anthropic_cua"

    def test_unknown_model_stays_vision_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_core.tools.computer_use_planners import resolve_planner_kind

        monkeypatch.setenv("KAZMA_CUA_PLANNER", "1")
        assert resolve_planner_kind("gpt-4o") == "vision_json"

    def test_adapt_anthropic_click(self) -> None:
        from kazma_core.tools.computer_use_planners import adapt_planner_payload

        a = adapt_planner_payload(
            "anthropic_cua",
            {"action": "left_click", "coordinate": [10, 20]},
        )
        assert a is not None
        assert a.action == "click"
        assert a.x == 10 and a.y == 20

    def test_adapt_unknown_falls_back(self) -> None:
        from kazma_core.tools.computer_use_planners import adapt_planner_payload

        assert adapt_planner_payload("anthropic_cua", {"foo": 1}) is None

    @pytest.mark.asyncio
    async def test_plan_without_cua_still_parses_json(self) -> None:
        async def fake(_messages):
            return {"action": "click", "x": 3, "y": 4}

        a = await plan_next_action("x", "aaaa", [], chat_fn=fake)
        assert a.action == "click"
        assert a.x == 3
