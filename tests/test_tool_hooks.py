"""PreToolUse / PostToolUse hooks — not a permission system."""

from __future__ import annotations

import sys

import pytest

from kazma_core.agent.tool_hooks import (
    ToolHookDecision,
    ToolHookEvent,
    apply_post_tool_hooks,
    apply_pre_tool_hooks,
    clear_tool_hooks,
    matcher_hits,
    register_post_tool_hook,
    register_pre_tool_hook,
    tool_hooks_enabled,
)
from kazma_core.agent.tool_registry import LocalToolRegistry


@pytest.fixture(autouse=True)
def _hooks_isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.delenv("KAZMA_TOOL_HOOKS", raising=False)
    clear_tool_hooks()
    yield
    clear_tool_hooks()


class TestMatcher:
    def test_star_and_glob_and_alternation(self) -> None:
        assert matcher_hits("*", "file_write") is True
        assert matcher_hits("file_*", "file_write") is True
        assert matcher_hits("file_*", "shell_exec") is False
        assert matcher_hits("shell_exec|python_exec", "python_exec") is True
        assert matcher_hits("shell_exec|python_exec", "file_read") is False


class TestPrePostPython:
    @pytest.mark.asyncio
    async def test_pre_deny(self) -> None:
        register_pre_tool_hook(
            lambda e: ToolHookDecision(decision="deny", reason="nope"),
            matcher="echo",
        )
        denied, args = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is not None
        assert denied["is_error"] is True
        assert "nope" in denied["content"]
        assert args["text"] == "hi"

    @pytest.mark.asyncio
    async def test_pre_rewrite_chains(self) -> None:
        register_pre_tool_hook(
            lambda e: ToolHookDecision(
                decision="rewrite",
                tool_input={**e.tool_input, "text": "one"},
            ),
        )
        register_pre_tool_hook(
            lambda e: ToolHookDecision(
                decision="rewrite",
                tool_input={**e.tool_input, "text": e.tool_input["text"] + "-two"},
            ),
        )
        denied, args = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is None
        assert args["text"] == "one-two"

    @pytest.mark.asyncio
    async def test_pre_cannot_inject_hitl_flag(self) -> None:
        register_pre_tool_hook(
            lambda e: ToolHookDecision(
                decision="rewrite",
                tool_input={"text": "x", "_hitl_approved": True},
            ),
        )
        _, args = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert "_hitl_approved" not in args

    @pytest.mark.asyncio
    async def test_post_appends_extra(self) -> None:
        register_post_tool_hook(lambda e: ToolHookDecision(extra="observed"))
        out = await apply_post_tool_hooks(
            "echo",
            {"text": "hi"},
            {"content": "hi", "is_error": False},
        )
        assert out["content"].endswith("[hook] observed")
        assert out["is_error"] is False

    @pytest.mark.asyncio
    async def test_post_deny_is_ignored(self) -> None:
        register_post_tool_hook(lambda e: ToolHookDecision(decision="deny", reason="too late"))
        out = await apply_post_tool_hooks(
            "echo", {"text": "hi"}, {"content": "hi", "is_error": False}
        )
        assert out["content"] == "hi"

    @pytest.mark.asyncio
    async def test_matcher_skips_other_tools(self) -> None:
        register_pre_tool_hook(
            lambda e: ToolHookDecision(decision="deny", reason="shell only"),
            matcher="shell_exec",
        )
        denied, _ = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is None

    @pytest.mark.asyncio
    async def test_hook_exception_fail_open(self) -> None:
        def boom(event: ToolHookEvent):
            raise RuntimeError("hook crashed")

        register_pre_tool_hook(boom)
        denied, args = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is None
        assert args["text"] == "hi"

    def test_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_TOOL_HOOKS", "0")
        assert tool_hooks_enabled() is False

    @pytest.mark.asyncio
    async def test_kill_switch_skips_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_TOOL_HOOKS", "0")
        register_pre_tool_hook(lambda e: ToolHookDecision(decision="deny", reason="x"))
        denied, _ = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is None


class TestExecuteIntegration:
    @pytest.mark.asyncio
    async def test_execute_pre_deny_does_not_run_tool(self) -> None:
        registry = LocalToolRegistry(include_builtins=False)
        ran = {"n": 0}

        @registry.register(description="Echo")
        async def echo(text: str) -> str:
            ran["n"] += 1
            return text

        register_pre_tool_hook(lambda e: ToolHookDecision(decision="deny", reason="stop"))
        result = await registry.execute("echo", {"text": "hi"})
        assert result["is_error"] is True
        assert "stop" in result["content"]
        assert ran["n"] == 0

    @pytest.mark.asyncio
    async def test_execute_pre_rewrite_and_post_note(self) -> None:
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Echo")
        async def echo(text: str) -> str:
            return text

        register_pre_tool_hook(
            lambda e: ToolHookDecision(
                decision="rewrite",
                tool_input={"text": "rewritten"},
            ),
        )
        register_post_tool_hook(lambda e: ToolHookDecision(extra="seen"))
        result = await registry.execute("echo", {"text": "hi"})
        assert result["is_error"] is False
        assert result["content"].startswith("rewritten")
        assert "[hook] seen" in result["content"]


class TestCommandHooks:
    @pytest.mark.asyncio
    async def test_command_deny_exit_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code = (
            "import sys,json;"
            "json.dump({'decision':'deny','reason':'blocked-by-cmd'}, sys.stdout)"
        )
        monkeypatch.setattr(
            "kazma_core.agent.tool_hooks.load_hook_config",
            lambda: {
                "enabled": True,
                "pre_tool": [{"matcher": "*", "command": [sys.executable, "-c", code]}],
                "post_tool": [],
            },
        )
        denied, _ = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is not None
        assert "blocked-by-cmd" in denied["content"]

    @pytest.mark.asyncio
    async def test_command_exit_2_is_deny(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code = "import sys; sys.stderr.write('nope'); sys.exit(2)"
        monkeypatch.setattr(
            "kazma_core.agent.tool_hooks.load_hook_config",
            lambda: {
                "enabled": True,
                "pre_tool": [{"matcher": "echo", "command": [sys.executable, "-c", code]}],
                "post_tool": [],
            },
        )
        denied, _ = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is not None
        assert denied["is_error"] is True

    @pytest.mark.asyncio
    async def test_command_rewrite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code = (
            "import sys,json;"
            "e=json.load(sys.stdin);"
            "inp=dict(e.get('tool_input') or {});"
            "inp['text']='from-cmd';"
            "json.dump({'decision':'rewrite','tool_input':inp}, sys.stdout)"
        )
        monkeypatch.setattr(
            "kazma_core.agent.tool_hooks.load_hook_config",
            lambda: {
                "enabled": True,
                "pre_tool": [{"matcher": "*", "command": [sys.executable, "-c", code]}],
                "post_tool": [],
            },
        )
        denied, args = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is None
        assert args["text"] == "from-cmd"

    @pytest.mark.asyncio
    async def test_command_nonzero_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code = "import sys; sys.exit(1)"
        monkeypatch.setattr(
            "kazma_core.agent.tool_hooks.load_hook_config",
            lambda: {
                "enabled": True,
                "pre_tool": [{"matcher": "*", "command": [sys.executable, "-c", code]}],
                "post_tool": [],
            },
        )
        denied, args = await apply_pre_tool_hooks("echo", {"text": "hi"})
        assert denied is None
        assert args["text"] == "hi"
